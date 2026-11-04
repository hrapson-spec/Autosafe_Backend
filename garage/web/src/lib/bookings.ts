import type { Booking } from '../api/types';
import { qualifiedBands, shownEstimate } from './estimate';

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

/** Only explicit ranking permission plus a positive safe-integer server ordinal permits priority.
 * Band labels never imply order. Missing/invalid keys stay in appointment order outside the ranked group,
 * including numerical estimates: a client fallback would mix incomparable orders with bands-only rows.
 */
const canRank = (b: Booking) => b.assessment?.display_policy?.ranking_enabled === true
  && Number.isSafeInteger(b.assessment?.rank_key) && (b.assessment?.rank_key ?? 0) > 0
  && !!(shownEstimate(b.assessment) || qualifiedBands(b.assessment));

export function groupBookings(bookings: Booking[]): GroupedBookings {
  const g: GroupedBookings = { ranked: [], notRanked: [], retests: [], serviceOnly: [], unsupported: [], cancelled: [] };
  for (const b of bookings) {
    if (b.status === 'cancelled') g.cancelled.push(b);
    else if (b.booking_type === 'retest') g.retests.push(b);
    else if (b.booking_type === 'service') g.serviceOnly.push(b);
    else if (stateOf(b) === 'unsupported') g.unsupported.push(b);
    else if (canRank(b)) g.ranked.push(b);
    else g.notRanked.push(b);
  }
  g.ranked.sort((a, b) => {
    const ka = a.assessment!.rank_key!;
    const kb = b.assessment!.rank_key!;
    return kb - ka || byTime(a, b);
  });
  for (const k of ['notRanked', 'retests', 'serviceOnly', 'unsupported', 'cancelled'] as const) g[k].sort(byTime);
  return g;
}
