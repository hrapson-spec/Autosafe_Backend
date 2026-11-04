import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { ApiError, api } from '../api/client';
import type { Booking } from '../api/types';
import { BookingListView } from '../components/BookingListView';
import { DateNav, type Window } from '../components/DateNav';
import { PageHeading } from '../components/Layout';
import { Alert, Spinner } from '../components/ui';
import { addDays, isIsoDate, todayLondon } from '../lib/dates';

export const BookingsPage: React.FC<{ pollMs?: number }> = ({ pollMs = 3000 }) => {
  const today = useMemo(() => todayLondon(), []);
  const [params, setParams] = useSearchParams();
  const startParam = params.get('start') ?? '';
  const daysParam = params.get('days');
  const win: Window = {
    start: isIsoDate(startParam) ? startParam : today,
    days: daysParam === '1' ? 1 : 7,
  };
  const [data, setData] = useState<Booking[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const seq = useRef(0);

  const load = useCallback(async () => {
    const mine = ++seq.current;
    try {
      const r = await api.bookings(win.start, addDays(win.start, win.days - 1));
      if (mine === seq.current) {
        setData(r);
        setError(null);
      }
    } catch (e) {
      if (mine === seq.current) setError(e instanceof ApiError ? e.message : 'Could not load bookings.');
    }
  }, [win.start, win.days]);

  useEffect(() => {
    setData(null);
    void load();
  }, [load]);

  const anyPending = data?.some((b) => (b.assessment?.state ?? 'pending') === 'pending' && b.status === 'active') ?? false;
  useEffect(() => {
    if (!anyPending) return;
    const t = setTimeout(() => void load(), pollMs);
    return () => clearTimeout(t);
  }, [anyPending, data, load, pollMs]);

  const change = (w: Window) => {
    const next = new URLSearchParams();
    if (!(w.start === today)) next.set('start', w.start);
    if (w.days === 1) next.set('days', '1');
    setParams(next);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <PageHeading>Bookings</PageHeading>
        <Link to="/app/import" className="inline-flex min-h-11 items-center rounded-lg bg-slate-900 px-4 py-2 text-sm font-semibold text-white">Import bookings</Link>
      </div>
      <DateNav window={win} today={today} onChange={change} />
      {error && <Alert kind="error">{error}</Alert>}
      {!data && !error && <Spinner label="Loading bookings" />}
      {data && data.length === 0 && (
        <Alert>No bookings in this window. Move the dates or <Link className="font-semibold underline" to="/app/import">import some</Link>.</Alert>
      )}
      {data && data.length > 0 && (
        <>
          {anyPending && <p role="status" className="text-sm text-slate-700">Some assessments are still running. This list updates by itself.</p>}
          <BookingListView bookings={data} linkFor={(b) => `/app/bookings/${b.id}`} />
        </>
      )}
    </div>
  );
};
