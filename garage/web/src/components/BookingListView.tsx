import React from 'react';
import { Link } from 'react-router-dom';
import type { Booking } from '../api/types';
import { groupBookings, stateOf } from '../lib/bookings';
import { formatDay, formatTime } from '../lib/dates';
import { BOOKING_TYPE_LABEL } from '../lib/importDraft';
import { AssessmentStatus } from './AssessmentStatus';
import { DECISION_LABEL } from './DecisionPanel';
import { EstimateDisplay } from './EstimateDisplay';
import { Card, Chip } from './ui';

const Row: React.FC<{ b: Booking; to: string; rank?: number; demo?: boolean }> = ({ b, to, rank, demo }) => (
  <li className="flex flex-wrap items-center gap-x-4 gap-y-2 py-3" data-testid="booking-row" data-reg={b.registration}>
    {rank !== undefined && <span className="w-6 text-lg font-bold text-slate-500" aria-label={`Rank ${rank}`}>{rank}</span>}
    <div className="min-w-[8rem] flex-1">
      <Link to={to} className="text-lg font-bold tracking-wide underline">{b.registration}</Link>
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm text-slate-700">{formatDay(b.appointment_date)}{formatTime(b.appointment_time) && <> <time data-testid="appointment-time">{formatTime(b.appointment_time)}</time></>}</span>
        <Chip>{BOOKING_TYPE_LABEL[b.booking_type]}</Chip>
        {b.rescheduled && <Chip tone="warn">Moved from {formatDay(b.original_appointment_date)}</Chip>}
        {demo && <Chip tone="warn">Fictional demo</Chip>}
        {b.decision && <Chip tone="ok">Decision: {DECISION_LABEL[b.decision.decision_type]}</Chip>}
      </div>
    </div>
    <div>
      {b.status === 'cancelled'
        ? <span className="text-sm text-slate-700">Cancelled: no estimate shown.</span>
        : stateOf(b) === 'available' && b.assessment?.result && (b.booking_type === 'mot' || b.booking_type === 'service_mot')
          ? <EstimateDisplay assessment={b.assessment} />
          : <AssessmentStatus assessment={b.assessment} />}
    </div>
  </li>
);

const Section: React.FC<{ id: string; title: string; note?: string; children: React.ReactNode; count: number }> = ({ id, title, note, children, count }) => (
  <Card as="section" aria-labelledby={id}>
    <h2 id={id} className="text-lg font-bold">{title} <span className="font-normal text-slate-600">({count})</span></h2>
    {note && <p className="mt-1 text-sm text-slate-700">{note}</p>}
    {children}
  </Card>
);

export const BookingListView: React.FC<{ bookings: Booking[]; linkFor: (b: Booking) => string; demo?: boolean }> = ({ bookings, linkFor, demo }) => {
  const g = groupBookings(bookings);
  const list = (items: Booking[], ranked = false) => (
    <ol className="mt-2 divide-y divide-slate-200" aria-label={ranked ? 'Ranked MOT bookings' : undefined}>
      {items.map((b, i) => <Row key={b.id} b={b} to={linkFor(b)} rank={ranked ? i + 1 : undefined} demo={demo} />)}
    </ol>
  );
  return (
    <div className="space-y-4">
      <Section
        id="sec-ranked"
        title="Initial MOTs, highest estimate first"
        count={g.ranked.length}
        note="Each percentage is the estimated chance of failing or needing rectification during the MOT, rounded by the service. It is built from recorded MOT history and is not an inspection. Bookings with the same percentage are ordered by the service's finer ranking, which is not shown."
      >
        {g.ranked.length === 0
          ? <p className="mt-2 text-sm text-slate-700">No MOT bookings with an available estimate in this window.</p>
          : list(g.ranked, true)}
      </Section>
      {g.notRanked.length > 0 && (
        <Section id="sec-unranked" title="Initial MOTs not ranked yet" count={g.notRanked.length} note="Waiting for an estimate, or one could not be produced.">{list(g.notRanked)}</Section>
      )}
      {g.retests.length > 0 && <Section id="sec-retests" title="Retests" count={g.retests.length} note="Kept apart and not ranked.">{list(g.retests)}</Section>}
      {g.serviceOnly.length > 0 && <Section id="sec-service" title="Service only" count={g.serviceOnly.length} note="Kept apart and not ranked.">{list(g.serviceOnly)}</Section>}
      {g.unsupported.length > 0 && (
        <Section id="sec-unsupported" title="Unsupported vehicles" count={g.unsupported.length} note="Outside the supported population, with the reason shown.">{list(g.unsupported)}</Section>
      )}
      {g.cancelled.length > 0 && <Section id="sec-cancelled" title="Cancelled" count={g.cancelled.length} note="Not assessed and not ranked.">{list(g.cancelled)}</Section>}
    </div>
  );
};
