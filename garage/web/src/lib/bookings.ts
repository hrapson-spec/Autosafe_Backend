import type { Booking } from '../api/types';
import { shownEstimate } from './estimate';

export interface GroupedBookings {
  ranked: Booking[];
  notRanked: Booking[];
  retests: Booking[];
  serviceOnly: Booking[];
  unsupported: Booking[];
  cancelled: Booking[];
}

export const stateOf = (b: Booking) => b.assessment?.state ?? 'pending';

const byTime = (a: Booking, b: Booking) =>
  a.appointment_date.localeCompare(b.appointment_date) || a.registration.localeCompare(b.registration);

/**
 * Cancelled, retest and service-only bookings are never ranked. MOT and
 * service + MOT bookings are ranked only when the service returned a rounded
 * percentage; ordering is by that percentage (descending). The service does
 * not expose an unrounded score, so ties fall back to date then registration.
 */
export function groupBookings(bookings: Booking[]): GroupedBookings {
  const g: GroupedBookings = { ranked: [], notRanked: [], retests: [], serviceOnly: [], unsupported: [], cancelled: [] };
  for (const b of bookings) {
    if (b.status === 'cancelled') g.cancelled.push(b);
    else if (b.booking_type === 'retest') g.retests.push(b);
    else if (b.booking_type === 'service') g.serviceOnly.push(b);
    else if (stateOf(b) === 'unsupported') g.unsupported.push(b);
    else if (shownEstimate(b.assessment)) g.ranked.push(b);
    else g.notRanked.push(b);
  }
  g.ranked.sort((a, b) => shownEstimate(b.assessment)!.percent - shownEstimate(a.assessment)!.percent || byTime(a, b));
  for (const k of ['notRanked', 'retests', 'serviceOnly', 'unsupported', 'cancelled'] as const) g[k].sort(byTime);
  return g;
}
