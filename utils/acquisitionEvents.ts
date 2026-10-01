/**
 * Acquisition measurement events (OA-004) -- interface and client plumbing
 * ONLY. Collection is OFF.
 *
 * Status: PROPOSED. The collector endpoint (`POST /api/acquisition/events`),
 * storage, session persistence, retention and legal basis all await the
 * OA-005 privacy decision. Until then this module has NO transport: it
 * builds typed events and hands them to a sink, and the default sink does
 * nothing. There is deliberately no fetch / sendBeacon / XHR / image beacon
 * here, and `ACQUISITION_COLLECTOR_ENABLED` is false.
 *
 * Safe-field rule (see docs/acquisition/EVENT_SCHEMA_v1.md): every value an
 * event carries is an enum, a boolean, or a random identifier. Never the
 * headline risk, a sample count, a report token or id, a registration,
 * postcode, make/model, URL, referrer, error message or free text.
 * `acquisitionEvents.test.ts` serialises every event type and asserts this.
 *
 * Deduplication state is MEMORY ONLY (session storage is unapproved): it
 * survives remounts and React StrictMode effect replays within one page
 * load, and does NOT survive a reload. Note that browsers keep
 * history.state across a reload, so a reloaded fresh-check report route
 * still carries its operation id and may emit a second result_rendered for
 * the same operation; the collector's per-session aggregation (not this
 * module) is what counts a session once.
 *
 * Acknowledgements are client-reported. They prove neither accuracy nor
 * model qualification.
 */
import type { ApiErrorCode, MatchScope, ResultKind } from '../types';
import type { OutcomeGroup } from './resultAcknowledgement';

/** Collection awaits the OA-005 privacy decision. No transport exists in this build. */
export const ACQUISITION_COLLECTOR_ENABLED: boolean = false;

export const ACQUISITION_SCHEMA_VERSION = 1;
/** Draft: the metric version is frozen before release (MEASUREMENT.md). */
export const ACQUISITION_METRIC_VERSION = 'oa-metric-v1-draft';

export type EntryMode = 'fresh_check' | 'restored_link';
export type PersistenceMode = 'saved' | 'inline_unsaved';
export type UnavailableReason = 'not_found' | 'expired' | 'unavailable' | 'error';
export type RenderFailedStage = 'lazy_load' | 'render' | 'contract_invalid';
export type CheckFailedStage = 'create_report';
export type SourceGroup =
  | 'google_organic'
  | 'other_search'
  | 'direct'
  | 'referral'
  | 'unknown'
  | 'internal_test';
export type ObservationState = 'observed' | 'consent_not_given' | 'unsupported';
export type CheckErrorCategory = ApiErrorCode | 'network_error' | 'invalid_response' | 'unknown';
/** Outcome groups a delivered result_rendered can carry; unavailable/error use other events. */
export type RenderedOutcomeGroup = Exclude<OutcomeGroup, 'unavailable' | 'error'>;

export const CHECK_ERROR_CATEGORIES: readonly CheckErrorCategory[] = [
  'invalid_registration',
  'vehicle_not_found',
  'dvsa_unavailable',
  'rate_limited',
  'internal_error',
  'report_not_found',
  'report_expired',
  'storage_unavailable',
  'idempotency_conflict',
  'undeclared_parameter',
  'network_error',
  'invalid_response',
  'unknown',
];

interface CommonFields {
  schema_version: typeof ACQUISITION_SCHEMA_VERSION;
  metric_version: string;
  /** Random per emitted event; the collector's idempotency key. */
  event_id: string;
}

export interface LandingObservedEvent extends CommonFields {
  event: 'landing_observed';
  /** Canonical public landing path from the OA-001 allowlist (not emitted in this branch). */
  landing_path: string;
  source_group: SourceGroup;
  observation_state: ObservationState;
}

export interface CheckStartedEvent extends CommonFields {
  event: 'check_started';
  operation_id: string;
  entry_mode: 'fresh_check';
}

export interface ReportCreatedEvent extends CommonFields {
  event: 'report_created';
  operation_id: string;
  entry_mode: 'fresh_check';
  result_kind: ResultKind;
  match_scope: MatchScope;
  persistence_mode: PersistenceMode;
}

export interface ResultRenderedEvent extends CommonFields {
  event: 'result_rendered';
  /** Absent for restored/shared links, which have no deliberate check. */
  operation_id?: string;
  entry_mode: EntryMode;
  persistence_mode: PersistenceMode;
  render_delivered: true;
  supported_result: boolean;
  outcome_group: RenderedOutcomeGroup;
  rate_valid: boolean;
  /** Omitted for model_prediction. */
  sample_nonzero?: boolean;
  scope_visible: boolean;
  result_kind: ResultKind;
  /** Never `unavailable`: that scope is reported as result_unavailable (D-004 precedence). */
  match_scope: Exclude<MatchScope, 'unavailable'>;
}

export interface ResultUnavailableEvent extends CommonFields {
  event: 'result_unavailable';
  operation_id?: string;
  entry_mode: EntryMode;
  reason: UnavailableReason;
}

export interface CheckFailedEvent extends CommonFields {
  event: 'check_failed';
  operation_id: string;
  entry_mode: 'fresh_check';
  error_category: CheckErrorCategory;
  stage: CheckFailedStage;
}

export interface RenderFailedEvent extends CommonFields {
  event: 'render_failed';
  operation_id?: string;
  entry_mode: EntryMode;
  stage: RenderFailedStage;
}

export type AcquisitionEvent =
  | LandingObservedEvent
  | CheckStartedEvent
  | ReportCreatedEvent
  | ResultRenderedEvent
  | ResultUnavailableEvent
  | CheckFailedEvent
  | RenderFailedEvent;

export type AcquisitionEventName = AcquisitionEvent['event'];

type DistributiveOmit<T, K extends PropertyKey> = T extends unknown ? Omit<T, K> : never;
export type AcquisitionEventInput = DistributiveOmit<AcquisitionEvent, keyof CommonFields>;

export interface AcquisitionSink {
  emit(event: AcquisitionEvent): void;
}

/** The default sink: drops every event. */
export const noopSink: AcquisitionSink = { emit: () => undefined };

let activeSink: AcquisitionSink = noopSink;

/**
 * Test-only sink injection. Not attached to `window`, not imported by any
 * production module (so a production bundle tree-shakes it away), and inert
 * in a production build even if reached.
 */
export function __setAcquisitionSinkForTests(sink: AcquisitionSink | null): void {
  if (import.meta.env.PROD) return;
  activeSink = sink ?? noopSink;
}

// ----------------------------------------------------------------------------
// Random identifiers (never derived from any input)
// ----------------------------------------------------------------------------

/** RFC 4122 v4-shaped identifier from the platform CSPRNG. */
export function randomId(): string {
  const c = globalThis.crypto;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  if (c && typeof c.getRandomValues === 'function') {
    const b = new Uint8Array(16);
    c.getRandomValues(b);
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    const h = Array.from(b, (x) => x.toString(16).padStart(2, '0')).join('');
    return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
  }
  // No secure randomness: refuse rather than fall back to a predictable id.
  throw new Error('secure random source unavailable');
}

/** A new operation id: one per deliberate check, reused only for a retry of the same unresolved operation. */
export function newOperationId(): string {
  return randomId();
}

const OPERATION_ID_PATTERN = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

/** Navigation state is untrusted input (history.state): accept only an id of the exact shape we mint. */
export function readOperationId(value: unknown): string | undefined {
  return typeof value === 'string' && OPERATION_ID_PATTERN.test(value) ? value : undefined;
}

// ----------------------------------------------------------------------------
// Emission
// ----------------------------------------------------------------------------

/**
 * Build and hand an event to the sink. Never throws: measurement must not
 * break the user flow it observes.
 */
export function emitAcquisitionEvent(input: AcquisitionEventInput): void {
  try {
    const event = {
      schema_version: ACQUISITION_SCHEMA_VERSION,
      metric_version: ACQUISITION_METRIC_VERSION,
      event_id: randomId(),
      ...input,
    } as AcquisitionEvent;
    activeSink.emit(event);
  } catch {
    // Intentionally swallowed.
  }
}

// ----------------------------------------------------------------------------
// In-memory completion markers (dedup). Memory only; see module comment.
// ----------------------------------------------------------------------------

const completionMarkers = new Set<string>();

/**
 * Claim a completion marker. Returns true the first time a (kind, key) pair
 * is seen in this page load and false afterwards. `key` is an operation id,
 * or a per-mount id for restored links.
 */
export function claimCompletion(kind: string, key: string): boolean {
  const marker = `${kind}:${key}`;
  if (completionMarkers.has(marker)) return false;
  completionMarkers.add(marker);
  return true;
}

export function __resetAcquisitionStateForTests(): void {
  if (import.meta.env.PROD) return;
  completionMarkers.clear();
  activeSink = noopSink;
}

/** Fixed-category mapping for a failed check. Never reads or returns an error message. */
export function checkErrorCategory(err: unknown): CheckErrorCategory {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === 'string' && (CHECK_ERROR_CATEGORIES as readonly string[]).includes(code)
    ? (code as CheckErrorCategory)
    : 'unknown';
}
