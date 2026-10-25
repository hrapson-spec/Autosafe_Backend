/**
 * Garage API types, base path /api/garage/v1.
 * Aligned by hand to garage/contract/openapi.yaml (1.0.0-draft.1). A drift test
 * (src/api/contract.test.ts) compares the enums and required fields below with
 * the YAML. Items marked PROPOSED are NOT in the contract.
 *
 * Contract gaps found while building the first pass (all handled gracefully):
 *  1. No briefing data: no dated source records, recorded advisories or follow-up
 *     questions. PROPOSED GET /bookings/{id}/briefing; a 404 renders an evidence gap.
 *  2. No feedback endpoint (helpful / not helpful). PROPOSED PUT /bookings/{id}/feedback.
 *  3. No demo-data endpoint. The fictional demo is client-side only (/demo).
 *  4. The rounding step is not returned, so copy says "rounded by the service".
 *  5. No unrounded score, so equal percentages tie-break by date then registration.
 *  6. No booking time of day; the list shows the date only.
 *  7. Import commit is one synchronous call: progress is indeterminate, not N of M.
 *  8. Email links go to server-rendered GET /auth/links/{token}; the SPA confirm
 *     page (/auth/confirm?token=) calls POST .../consume and duplicates that page.
 *  9. Decision option labels and help text are client copy for the four enum values.
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
  percent_rounded: number;
  band: Band;
}

export interface Assessment {
  id: string;
  booking_id: string;
  version: number;
  state: AssessmentState;
  reason?: string | null;
  reason_text?: string | null;
  result?: AssessmentResult | null;
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

export interface ImportPreview {
  import_id: string;
  status: 'previewed';
  counts: ImportCounts;
  rows: PreviewRow[];
  expires_at: string;
}

export interface ImportResult {
  import_id: string;
  status: 'committed';
  already_committed: boolean;
  counts: ImportCounts;
  created_booking_ids: string[];
  committed_at?: string;
}

// ---- PROPOSED, not in the contract -------------------------------------

/** PROPOSED: GET /bookings/{id}/briefing. Dated source records and rule-generated follow-up questions. */
export interface SourceRecord {
  test_date: string;
  test_result: 'PASSED' | 'FAILED' | 'PRS' | 'ABANDONED' | 'ABORTED';
  odometer_value: number | null;
  odometer_unit: 'mi' | 'km' | null;
  defects: Array<{ type: 'ADVISORY' | 'MINOR' | 'MAJOR' | 'DANGEROUS' | 'PRS' | 'FAIL' | 'USER_ENTERED'; text: string }>;
}
export interface FollowUpQuestion {
  id: string;
  rule_id: string;
  text: string;
  source_test_date: string | null;
}
export interface BriefingExtras {
  source_records: SourceRecord[];
  follow_up_questions: FollowUpQuestion[];
}

/** PROPOSED: PUT /bookings/{id}/feedback. */
export type FeedbackValue = 'helpful' | 'not_helpful';

export interface ApiErrorBody {
  error: { code: string; message: string; details?: Record<string, unknown> | null };
}

export const MAX_IMPORT_ROWS = 200;
