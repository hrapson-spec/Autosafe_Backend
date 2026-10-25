/**
 * Fixtures for the mock API and for the public fictional demo. Everything is
 * invented: registrations are DEMOnn (not real plates), no customer fields
 * exist, and dates are generated relative to "today" (Europe/London).
 *
 * `bandFor` / `roundTo5` below stand in for the SERVER's display decision
 * (contract: percent_rounded and band come from the API). They live under
 * mocks/ only; production code never rounds or bands.
 */
import type { Assessment, AssessmentState, Band, Booking, BookingType, BriefingExtras, SourceRecord } from '../api/types';
import { addDays } from '../lib/dates';

export const roundTo5 = (p: number) => Math.min(100, Math.round((p * 100) / 5) * 5);
export const bandFor = (percent: number): Band => (percent >= 35 ? 'high' : percent >= 20 ? 'medium' : 'low');

export interface AssessmentOpts {
  percent?: number;
  reason?: string | null;
  reasonText?: string | null;
  version?: number;
  trigger?: Assessment['trigger'];
  today: string;
  priorCount?: number;
  latestPrior?: string | null;
}

export function makeAssessment(bookingId: string, state: AssessmentState, o: AssessmentOpts): Assessment {
  const available = state === 'available';
  const version = o.version ?? 1;
  return {
    id: `${bookingId}-a${version}`,
    booking_id: bookingId,
    version,
    state,
    reason: available ? null : (o.reason ?? null),
    reason_text: available ? null : (o.reasonText ?? null),
    result: available && o.percent !== undefined ? { percent_rounded: o.percent, band: bandFor(o.percent) } : null,
    evidence: available
      ? { prior_test_count: o.priorCount ?? 3, latest_prior_test_date: o.latestPrior ?? addDays(o.today, -365), history_fetched_at: `${o.today}T07:00:00+00:00` }
      : null,
    trigger: o.trigger ?? 'import',
    input_cutoff_date: o.today,
    input_cutoff_at: `${o.today}T07:00:00+00:00`,
    provider: { name: 'mock-provider', version: 'mock-0 (not a real model)' },
    retry_at: null,
    created_at: `${o.today}T07:00:00+00:00`,
    completed_at: state === 'pending' ? null : `${o.today}T07:00:05+00:00`,
  };
}

interface DemoSpec {
  reg: string;
  offset: number;
  type: BookingType;
  state: AssessmentState;
  p?: number;
  reason?: string;
  text?: string;
  cancelled?: boolean;
}

export const DEMO_SPECS: DemoSpec[] = [
  { reg: 'DEMO01', offset: 0, type: 'mot', state: 'available', p: 0.463 },
  { reg: 'DEMO02', offset: 0, type: 'mot', state: 'available', p: 0.312 },
  { reg: 'DEMO03', offset: 1, type: 'mot', state: 'available', p: 0.118 },
  { reg: 'DEMO04', offset: 1, type: 'mot', state: 'available', p: 0.274 },
  { reg: 'DEMO05', offset: 2, type: 'mot', state: 'pending', reason: 'upstream_retrying', text: 'Fetching recorded MOT history.' },
  { reg: 'DEMO06', offset: 2, type: 'mot', state: 'unavailable', reason: 'upstream_unavailable', text: 'The recorded MOT history could not be retrieved. Try refreshing the assessment.' },
  { reg: 'DEMO07', offset: 3, type: 'mot', state: 'unsupported', reason: 'no_prior_mot', text: 'No earlier MOT test is recorded for this vehicle, so no estimate is made.' },
  { reg: 'DEMO08', offset: 3, type: 'mot', state: 'failed', reason: 'provider_error', text: 'The assessment did not complete. Refresh to try again.' },
  { reg: 'DEMO09', offset: 4, type: 'retest', state: 'unsupported', reason: 'retest_booking', text: 'Retests are not assessed.' },
  { reg: 'DEMO10', offset: 4, type: 'service', state: 'unsupported', reason: 'service_only', text: 'Service-only bookings are not assessed.' },
  { reg: 'DEMO11', offset: 5, type: 'mot', state: 'unsupported', reason: 'not_class_4', text: 'This vehicle is not recognised as a supported Class 4 model.' },
  { reg: 'DEMO12', offset: 5, type: 'service_mot', state: 'available', p: 0.221 },
  { reg: 'DEMO13', offset: 6, type: 'mot', state: 'available', p: 0.4, cancelled: true },
];

export function demoBookings(today: string): Booking[] {
  return DEMO_SPECS.map((s, i) => {
    const id = `demo-${i + 1}`;
    const date = addDays(today, s.offset);
    return {
      id,
      registration: s.reg,
      appointment_date: date,
      original_appointment_date: date,
      booking_type: s.type,
      status: s.cancelled ? 'cancelled' : 'active',
      source: 'import',
      rescheduled: false,
      created_at: `${today}T07:00:00+00:00`,
      assessment: makeAssessment(id, s.state, { percent: s.p === undefined ? undefined : roundTo5(s.p), reason: s.reason, reasonText: s.text, today }),
      decision: null,
    };
  });
}

function recordsFor(today: string, seed: number): SourceRecord[] {
  return [
    {
      test_date: addDays(today, -365 - seed), test_result: 'PASSED', odometer_value: 41200 + seed * 311, odometer_unit: 'mi',
      defects: [
        { type: 'ADVISORY', text: 'Front brake disc worn, pitted or scored, but not seriously weakened' },
        { type: 'ADVISORY', text: 'Tyre close to the legal limit on the nearside front' },
      ],
    },
    {
      test_date: addDays(today, -730 - seed), test_result: 'FAILED', odometer_value: 29800 + seed * 297, odometer_unit: 'mi',
      defects: [
        { type: 'MAJOR', text: 'Headlamp aim too high' },
        { type: 'ADVISORY', text: 'Corrosion on the underside of the sill' },
      ],
    },
    { test_date: addDays(today, -1095 - seed), test_result: 'PASSED', odometer_value: 18400 + seed * 250, odometer_unit: 'mi', defects: [] },
  ];
}

/** Mock stand-in for the PROPOSED server-side briefing rules (R8: deterministic templates, no LLM). */
export function briefingExtrasFor(booking: Booking, today: string, seed: number): BriefingExtras {
  if (booking.assessment?.state !== 'available') return { source_records: [], follow_up_questions: [] };
  const records = recordsFor(today, seed);
  return {
    source_records: records,
    follow_up_questions: [
      { id: 'q1', rule_id: 'prior_advisory_brakes', source_test_date: records[0].test_date, text: 'Brake disc wear was advised at the last recorded test. Have the discs been checked or replaced since?' },
      { id: 'q2', rule_id: 'prior_advisory_tyre', source_test_date: records[0].test_date, text: 'A tyre was close to the legal limit at the last recorded test. What is the tread depth now?' },
      { id: 'q3', rule_id: 'prior_fail_lighting', source_test_date: records[1].test_date, text: 'Headlamp aim failed two tests ago. Has the aim been checked since?' },
    ],
  };
}
