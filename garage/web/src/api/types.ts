/**
 * Garage API types, base path /api/garage/v1.
 * Aligned by hand to garage/contract/openapi.yaml (1.0.0-draft.2). A drift test
 * (src/api/contract.test.ts) compares the enums, required fields and endpoints below with the YAML.
 *
 * The nine gaps the first pass reported are closed in draft.2:
 *  1. Briefing: GET /bookings/{id}/briefing (rule-generated, dated source records, evidence gaps, follow-ups).
 *  2. Feedback: PUT /bookings/{id}/feedback.
 *  3. Demo data stays client-side and fictional (src/mocks/fixtures.ts, /demo); the API never serves it.
 *  4. display_policy on completed assessments: rounding step, band edges and labels, marked provisional.
 *  5. rank_key: order-only server key; ranking never relies on rounded values and the score is never sent.
 *  6. appointment_time (HH:MM) on bookings and import rows.
 *  7. Import commit is asynchronous: a job, then GET /imports/{id} reports processed of total.
 *  8. The server-rendered confirm page is the only confirm page; the SPA has no /auth/confirm route.
 *  9. Decision option labels and help text remain client copy for the four enum values.
 *
 * Data rule: no customer name, email or phone anywhere. Staff have an email
 * because they sign in by email link.
 */

export type Role = 'owner' | 'staff';
export type BookingType = 'mot' | 'service' | 'retest' | 'service_mot';
export type BookingStatus = 'active' | 'cancelled';
export type AssessmentState = 'pending' | 'available' | 'unavailable' | 'unsupported' | 'failed';
export type Band = 'low' | 'medium' | 'high';
export type DecisionType = 'check_repair_history' | 'offer_inspection' | 'adjust_booking_plan' | 'proceed_as_booked';

export interface Me {
  user: { id: string; email: string };
  workshop: { id: string; name: string };
  role: Role;
  membership_id?: string;
  memberships: Array<{ workshop_id: string; workshop_name: string; role: Role }>;
}

export interface Usage {
  date: string;
  assessments_used: number;
  assessments_limit: number;
  assessments_remaining: number;
}

export interface Workshop {
  id: string;
  name: string;
  usage: Usage;
}

export interface Member {
  membership_id: string;
  email: string;
  role: Role;
  status: 'active';
  created_at: string;
}

/** Display precision is decided by the server: the client never computes a percentage or a band (R7). */
export interface AssessmentResult {
  percent_rounded: number | null;
  band: Band;
}

/** How the service turned a probability into what is shown. The client labels what it is given (R7). */
export interface DisplayPolicy {
  rounding_pp: number;
  /** On the rounded scale: low below edges[0], medium from edges[0] to below edges[1], high from edges[1]. */
  band_edges: [number, number];
  band_labels: Record<Band, string>;
  /** True while the values are the stub's, not a frozen model package's. */
  provisional: boolean;
  display_state?: 'unavailable' | 'shadow' | 'bands' | 'simulated' | 'percentage';
  operational_qualified?: boolean;
  ranking_enabled?: boolean;
  policy_version?: string;
  model_version?: string | null;
  suppression_reason?: string | null;
}

export interface Assessment {
  id: string;
  booking_id: string;
  version: number;
  state: AssessmentState;
  reason?: string | null;
  reason_text?: string | null;
  result?: AssessmentResult | null;
  display_policy?: DisplayPolicy | null;
  /** List-cohort ordinal (larger = higher estimated risk), across all filtered bookings before pagination. Detail routes return null. Not a probability. */
  rank_key?: number | null;
  evidence?: {
    prior_test_count?: number;
    latest_prior_test_date?: string | null;
    history_fetched_at?: string;
  } | null;
  trigger: 'import' | 'manual_entry' | 'refresh';
  input_cutoff_date: string;
  input_cutoff_at?: string;
  provider?: { name?: string; version?: string } | null;
  retry_at?: string | null;
  created_at: string;
  completed_at?: string | null;
}

export interface Actor {
  membership_id: string;
  email: string;
  role: Role;
}

export interface Decision {
  id: string;
  booking_id: string;
  decision_type: DecisionType;
  note?: string | null;
  actor: Actor;
  assessment_id?: string | null;
  supersedes_id?: string | null;
  created_at: string;
}

export interface Booking {
  id: string;
  registration: string;
  appointment_date: string;
  /** "HH:MM" 24-hour London, or null (untimed). */
  appointment_time?: string | null;
  original_appointment_date: string;
  booking_type: BookingType;
  status: BookingStatus;
  source: 'import' | 'manual';
  rescheduled?: boolean;
  created_at: string;
  assessment?: Assessment | null;
  decision?: Decision | null;
  outcome?: unknown;
}

export interface BookingPage {
  items: Booking[];
  next_cursor: string | null;
}

// ---- imports ----------------------------------------------------------

export interface ImportRow {
  registration: string;
  date: string;
  type: string;
  /** Optional appointment time, 24-hour. */
  time?: string | null;
}

export type RowStatus = 'new' | 'duplicate_existing' | 'duplicate_in_batch' | 'invalid';

export interface RowIssue {
  code: string;
  field?: string | null;
  message: string;
}

export interface PreviewRow {
  row_number: number;
  status: RowStatus;
  registration?: string | null;
  appointment_date?: string | null;
  appointment_time?: string | null;
  booking_type?: BookingType | null;
  existing_booking_id?: string | null;
  duplicate_of_row?: number | null;
  errors: RowIssue[];
  warnings: RowIssue[];
}

export interface ImportCounts {
  total: number;
  new: number;
  duplicate_existing: number;
  duplicate_in_batch: number;
  invalid: number;
}

export interface ImportProgress {
  processed: number;
  total: number;
}

export interface ImportPreview {
  import_id: string;
  status: 'previewed';
  counts: ImportCounts;
  rows: PreviewRow[];
  progress?: ImportProgress | null;
  expires_at: string;
}

export const IMPORT_STATUSES = ['previewed', 'committing', 'committed', 'failed', 'expired'] as const;
export type ImportStatus = (typeof IMPORT_STATUSES)[number];

/** An import in any state: GET /imports/{id}, and the answer to POST /imports/{id}/commit (202, or 200 once finished). */
export interface ImportRecord {
  import_id: string;
  status: ImportStatus;
  already_committed?: boolean;
  counts: ImportCounts;
  rows: PreviewRow[];
  progress?: ImportProgress | null;
  created_booking_ids?: string[];
  failure_reason?: string;
  expires_at?: string;
  committed_at?: string | null;
}

// ---- briefing and feedback ------------------------------------------------

export const BRIEFING_STATUSES = ['ready', 'pending', 'unavailable'] as const;
export type BriefingStatus = (typeof BRIEFING_STATUSES)[number];
export const BRIEFING_RESULTS = ['PASSED', 'FAILED', 'RECTIFIED'] as const;
export type BriefingResult = (typeof BRIEFING_RESULTS)[number];

export interface BriefingDefect {
  /** ADVISORY, MINOR, MAJOR, DANGEROUS, PRS (rectified at the test) or another value the source used. */
  type: string;
  dangerous: boolean;
  /** The source's wording, verbatim: a quotation of a past record, not a statement about today. */
  text: string;
  recorded_on: string;
  /** e.g. "Advisory, recorded at test on 21 July 2026". */
  label: string;
}

export interface BriefingRecord {
  test_date: string;
  result: BriefingResult;
  odometer_value: number | null;
  odometer_unit: 'MI' | 'KM' | null;
  defects: BriefingDefect[];
}

export interface BriefingGap {
  rule_id: string;
  text: string;
}

export interface BriefingQuestion {
  id: string;
  rule_id: string;
  text: string;
  source_test_date: string | null;
}

export const FEEDBACK_VALUES = ['helpful', 'not_helpful'] as const;
export type FeedbackValue = (typeof FEEDBACK_VALUES)[number];
export const FEEDBACK_REASONS = ['inaccurate_records', 'not_relevant', 'too_long', 'missing_information', 'other'] as const;
export type FeedbackReason = (typeof FEEDBACK_REASONS)[number];

export interface Feedback {
  value: FeedbackValue;
  reason_code?: FeedbackReason | null;
  briefing_version: string;
  assessment_version: number;
  updated_at: string;
}

export interface Briefing {
  booking_id: string;
  status: BriefingStatus;
  reason?: string | null;
  reason_text?: string | null;
  rule_table_version: string;
  briefing_version: string | null;
  assessment_id?: string | null;
  assessment_version?: number | null;
  history_cutoff_date?: string | null;
  history_cutoff_at?: string | null;
  source?: { snapshot_sha256: string; upstream_status: 'ok' | 'not_found'; fetched_at: string } | null;
  /** Template text from fixed rules. Never mentions an estimate. */
  summary: string | null;
  source_records: BriefingRecord[];
  evidence_gaps: BriefingGap[];
  follow_up_questions: BriefingQuestion[];
  /** The calling member's own feedback on this briefing version. */
  feedback: Feedback | null;
}

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> | null };
}

export const MAX_IMPORT_ROWS = 200;
