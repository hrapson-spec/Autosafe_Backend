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

/** Date, then appointment time (untimed last), then registration. */
const byTime = (a: Booking, b: Booking) =>
  a.appointment_date.localeCompare(b.appointment_date)
  || (a.appointment_time ?? '24:00').localeCompare(b.appointment_time ?? '24:00')
  || a.registration.localeCompare(b.registration);

/**
 * Cancelled, retest and service-only bookings are never ranked. MOT and service + MOT bookings are ranked
 * only when the service returned a rounded percentage. Order: the service's `rank_key` (descending; it
 * separates equal rounded percentages and is never shown); when a key is missing, the rounded percentage;
 * then date, time and registration. The unrounded score is never available to the client.
 */
export function groupBookings(bookings: Booking[]): GroupedBookings {
  const g: GroupedBookings = { ranked: [], notRanked: [], retests: [], serviceOnly: [], unsupported: [], cancelled: [] };
  for (const b of bookings) {
    if (b.status === 'cancelled') g.cancelled.push(b);
    else if (b.booking_type === 'retest') g.retests.push(b);
    else if (b.booking_type === 'service') g.serviceOnly.push(b);
    else if (stateOf(b) === 'unsupported') g.unsupported.push(b);
    else if (shownEstimate(b.assessment) && b.assessment?.display_policy?.ranking_enabled === true) g.ranked.push(b);
    else g.notRanked.push(b);
  }
  g.ranked.sort((a, b) => {
    const ka = a.assessment?.rank_key;
    const kb = b.assessment?.rank_key;
    if (ka != null && kb != null && ka !== kb) return kb - ka;      // the service's order, whatever the rounding hides
    return shownEstimate(b.assessment)!.percent - shownEstimate(a.assessment)!.percent || byTime(a, b);
  });
  for (const k of ['notRanked', 'retests', 'serviceOnly', 'unsupported', 'cancelled'] as const) g[k].sort(byTime);
  return g;
}
