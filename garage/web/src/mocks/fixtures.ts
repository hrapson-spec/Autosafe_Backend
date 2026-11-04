/**
 * Fixtures for the mock API and for the public fictional demo. Everything is
 * invented: registrations are DEMOnn (not real plates), no customer fields
 * exist, and dates are generated relative to "today" (Europe/London).
 *
 * `bandFor` / `roundTo5` below stand in for the SERVER's display decision
 * (contract: percent_rounded and band come from the API). They live under
 * mocks/ only; production code never rounds or bands.
 */
import type { Assessment, AssessmentState, Band, Booking, BookingType, Briefing, BriefingRecord, DisplayPolicy } from '../api/types';
import { addDays } from '../lib/dates';

export const roundTo5 = (p: number) => Math.min(100, Math.round((p * 100) / 5) * 5);
export const bandFor = (percent: number): Band => (percent >= 35 ? 'high' : percent >= 20 ? 'medium' : 'low');

/** Mock stand-in for the stub provider's provisional policy (same values as garage/api/provider.py). */
export const MOCK_POLICY: DisplayPolicy = { rounding_pp: 5, band_edges: [20, 35], band_labels: { low: 'Low', medium: 'Medium', high: 'High' }, provisional: true, display_state: 'simulated', ranking_enabled: true, operational_qualified: false, policy_version: 'fixture-v1', model_version: 'invented-fixture', suppression_reason: null };

/** Mock stand-in for the server's order-only key: strictly increasing in the (hidden) unrounded score. */
export const mockRankKey = (score: number) => Math.round(1e9 * Math.min(Math.max(score, 0), 1) ** 1.7);

export interface AssessmentOpts {
  percent?: number;
  /** Mock only: the unrounded probability the server would hold. Only its order survives, as rank_key. */
  score?: number;
  reason?: string | null;
  reasonText?: string | null;
  version?: number;
  trigger?: Assessment['trigger'];
  today: string;
  priorCount?: number;
  latestPrior?: string | null;
}

const providerRan = (state: AssessmentState, reason?: string | null) =>
  state === 'available' || state === 'failed' || (state === 'unavailable' && reason !== 'booking_cancelled')
  || (state === 'unsupported' && reason !== 'service_only' && reason !== 'retest_booking');

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
    display_policy: providerRan(state, o.reason) ? MOCK_POLICY : null,
    rank_key: available && o.percent !== undefined ? mockRankKey(o.score ?? o.percent / 100) : null,
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
      assessment: makeAssessment(id, s.state, { percent: s.p === undefined ? undefined : roundTo5(s.p), score: s.p, reason: s.reason, reasonText: s.text, today }),
      decision: null,
    };
  });
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
const longDate = (iso: string) => `${Number(iso.slice(8, 10))} ${MONTHS[Number(iso.slice(5, 7)) - 1]} ${iso.slice(0, 4)}`;
const label = (type: string, iso: string) => `${type}, recorded at test on ${longDate(iso)}`;

function demoRecords(today: string, seed: number): BriefingRecord[] {
  const d1 = addDays(today, -365 - seed);
  const d2 = addDays(today, -730 - seed);
  const d3 = addDays(today, -1095 - seed);
  return [
    {
      test_date: d1, result: 'PASSED', odometer_value: 41200 + seed * 311, odometer_unit: 'MI',
      defects: [
        { type: 'ADVISORY', dangerous: false, text: 'Front brake disc worn, pitted or scored, but not seriously weakened', recorded_on: d1, label: label('Advisory', d1) },
        { type: 'ADVISORY', dangerous: false, text: 'Tyre close to the legal limit on the nearside front', recorded_on: d1, label: label('Advisory', d1) },
      ],
    },
    {
      test_date: d2, result: 'FAILED', odometer_value: 29800 + seed * 297, odometer_unit: 'MI',
      defects: [
        { type: 'MAJOR', dangerous: false, text: 'Headlamp aim too high', recorded_on: d2, label: label('Major defect', d2) },
        { type: 'ADVISORY', dangerous: false, text: 'Corrosion on the underside of the sill', recorded_on: d2, label: label('Advisory', d2) },
      ],
    },
    { test_date: d3, result: 'PASSED', odometer_value: 18400 + seed * 250, odometer_unit: 'MI', defects: [] },
  ];
}

/**
 * FICTIONAL client-side briefing for the public demo and the DEMOnn mock bookings. The API never serves demo
 * data (contract gap 3); real-shape briefings in the mock API come from briefingFixtures.json instead.
 */
export function demoBriefingFor(booking: Booking, today: string, seed: number): Briefing {
  const a = booking.assessment;
  const base = {
    booking_id: booking.id, rule_table_version: 'briefing-rules-1', briefing_version: null, assessment_id: null, assessment_version: null,
    history_cutoff_date: null, history_cutoff_at: null, source: null, summary: null,
    source_records: [], evidence_gaps: [], follow_up_questions: [], feedback: null,
  };
  if (a?.state !== 'available') {
    return { ...base, status: a?.state === 'pending' ? 'pending' : 'unavailable', reason: a?.reason ?? null, reason_text: a?.reason_text ?? null };
  }
  const records = demoRecords(today, seed);
  const last = records[0].test_date;
  return {
    ...base, status: 'ready', reason: null, reason_text: null,
    briefing_version: 'demo0000demo0000.briefing-rules-1', assessment_id: a.id, assessment_version: a.version,
    history_cutoff_date: today, history_cutoff_at: `${today}T07:00:00+00:00`,
    summary: `Recorded before ${longDate(today)}: 3 MOT test results. The most recent, on ${longDate(last)}, was a pass at ${records[0].odometer_value!.toLocaleString('en-GB')} miles. Listed below: no evidence gaps and 2 follow-up questions, generated by fixed rules over the recorded history.`,
    source_records: records,
    follow_up_questions: [
      { id: 'Q05-demo0001', rule_id: 'Q05_last_test_advisory', source_test_date: last, text: `Ask whether the \u201cFront brake disc worn, pitted or scored, but not seriously weakened\u201d advisory from ${longDate(last)} has been addressed.` },
      { id: 'Q05-demo0002', rule_id: 'Q05_last_test_advisory', source_test_date: last, text: `Ask whether the \u201cTyre close to the legal limit on the nearside front\u201d advisory from ${longDate(last)} has been addressed.` },
    ],
  };
}
