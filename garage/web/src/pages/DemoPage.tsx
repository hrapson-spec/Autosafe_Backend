import React, { useMemo, useState } from 'react';
import { Link, useParams } from 'react-router-dom';
import type { Decision, DecisionType } from '../api/types';
import { BookingListView } from '../components/BookingListView';
import { BriefingView } from '../components/BriefingView';
import { PageHeading } from '../components/Layout';
import { Alert } from '../components/ui';
import { todayLondon } from '../lib/dates';
import { demoBookings, demoBriefingFor } from '../mocks/fixtures';

/**
 * Public fictional demo. Same components as the workspace over built-in
 * fictional data (registrations DEMOnn). Nothing is sent anywhere, and
 * decisions live in memory only. The demo is NOT served by the API (contract gap 3).
 */
export const DemoListPage: React.FC = () => {
  const today = useMemo(() => todayLondon(), []);
  const bookings = useMemo(() => demoBookings(today), [today]);
  return (
    <div className="space-y-4">
      <PageHeading>Fictional demo</PageHeading>
      <Alert>These bookings, registrations and figures are invented to show how the service works. They describe no real vehicle.</Alert>
      <BookingListView bookings={bookings} linkFor={(b) => `/demo/briefing/${b.id}`} demo />
      <p><Link to="/sign-in" className="font-semibold underline">{import.meta.env.VITE_MOCK_API ? 'Create a demo workshop' : 'Create a workshop to use real bookings'}</Link></p>
    </div>
  );
};

export const DemoBriefingPage: React.FC = () => {
  const { id } = useParams();
  const today = useMemo(() => todayLondon(), []);
  const booking = useMemo(() => demoBookings(today).find((b) => b.id === id), [today, id]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  if (!booking) return <Alert kind="error">That demo booking does not exist. <Link className="underline" to="/demo">Back to the demo</Link></Alert>;
  const briefing = demoBriefingFor(booking, today, Number(booking.id.replace(/\D/g, '')));
  const onDecide = async (decision_type: DecisionType, note: string) => {
    setDecisions((d) => [{
      id: `local-${d.length}`, booking_id: booking.id, decision_type, note: note || null,
      actor: { membership_id: 'demo', email: 'you (demo)', role: 'owner' }, created_at: new Date().toISOString(),
    }, ...d]);
  };
  return <BriefingView booking={booking} decisions={decisions} briefing={briefing} backTo="/demo" onDecide={onDecide} onFeedback={async () => {}} demo />;
};
