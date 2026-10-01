/**
 * Acquisition measurement events (OA-004 interface) and first-party transport
 * (OA-005, DECISIONS.md D-005). Collection is OFF.
 *
 * `ACQUISITION_COLLECTOR_ENABLED` is false. Until it is true no transport is
 * installed: events go to the default no-op sink and nothing is sent. The
 * flag flips only at the D-005 enable gate (docs/acquisition/COLLECTOR.md).
 *
 * When enabled, the sink POSTs each event once (one retry on network error or
 * 5xx within 10 s, same event_id; never on 4xx/429) to the same-origin
 * `/api/acquisition/events` with keepalive, no credentials, no referrer and no
 * cache. There is no queue and no persistence: no cookies, localStorage,
 * sessionStorage or IndexedDB. `navigator.globalPrivacyControl === true`
 * sends nothing. Every identifier is random, per page load, memory only, and
 * never derived from an input: `session_id` (one per document),
 * `operation_id` (one per deliberate check), `landing_id` (see
 * utils/acquisitionLanding.ts).
 *
 * Safe-field rule (see docs/acquisition/EVENT_SCHEMA_v1.md): every value an
 * event carries is an enum, a boolean, or a random identifier. Never the
 * headline risk, a sample count, a report token or id, a registration,
 * postcode, make/model, URL, referrer, error message or free text.
 * `acquisitionEvents.test.ts` serialises every event type and asserts this.
 *
 * Deduplication state is MEMORY ONLY: it survives remounts and React
 * StrictMode effect replays within one page load, and does NOT survive a
 * reload. Note that browsers keep history.state across a reload, so a
 * reloaded fresh-check report route still carries its operation id and may
 * emit a second result_rendered for the same operation; the collector's
 * per-landing aggregation (not this module) counts a landing once.
 *
 * Acknowledgements are client-reported. They prove neither accuracy nor
 * model qualification.
 */
import type { ApiErrorCode, MatchScope, ResultKind } from '../types';
import type { OutcomeGroup } from './resultAcknowledgement';

/**
 * Collection is OFF. Flipped to true only at the D-005 enable gate. Rollback
 * is flipping it back to false (and unsetting ACQUISITION_INGEST_ENABLED on
 * the server).
 */
export const ACQUISITION_COLLECTOR_ENABLED: boolean = false;

export const ACQUISITION_ENDPOINT = '/api/acquisition/events';

export const ACQUISITION_SCHEMA_VERSION = 1;
/** Draft: the metric version is frozen before release (MEASUREMENT.md). */
export const ACQUISITION_METRIC_VERSION = 'oa-metric-v1-draft';

export type EntryMode = 'fresh_check' | 'restored_link';
export type PersistenceMode = 'saved' | 'inline_unsaved';
export type UnavailableReason = 'not_found' | 'expired' | 'unavailable' | 'error';
export type RenderFailedStage = 'lazy_load' | 'render' | 'contract_invalid';
export type CheckFailedStage = 'create_report';
/**
 * `internal` is a same-site referrer (never an organic landing);
 * `paid_search` is a landing whose URL carried a paid-click marker (D-006; the
 * marker itself is never sent); `internal_test` is synthetic/test traffic.
 */
export type SourceGroup =
  | 'google_organic'
  | 'other_search'
  | 'direct'
  | 'referral'
  | 'unknown'
  | 'internal'
  | 'paid_search'
  | 'internal_test';
/** Allowlisted landing-page families (D-005); the landing path itself is never sent. */
export type PageFamily =
  | 'home'
  | 'app'
  | 'guide'
  | 'make'
  | 'model'
  | 'comparison'
  | 'pillar'
  | 'problem_hub'
  | 'other_public';
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
  /** Allowlisted page family of the landing page (D-005); never the path. */
  page_family: PageFamily;
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
  context = { ...DEFAULT_CONTEXT };
}

/** Fixed-category mapping for a failed check. Never reads or returns an error message. */
export function checkErrorCategory(err: unknown): CheckErrorCategory {
  const code = (err as { code?: unknown } | null)?.code;
  return typeof code === 'string' && (CHECK_ERROR_CATEGORIES as readonly string[]).includes(code)
    ? (code as CheckErrorCategory)
    : 'unknown';
}

// ----------------------------------------------------------------------------
// Context (landing attribution) and first-party transport (OA-005, D-005)
// ----------------------------------------------------------------------------

/** Build-time release identity (vite `define`); absent in dev/test unless the build sets it. */
declare const __RELEASE_SHA__: string | undefined;

const RELEASE_SHA_PATTERN = /^[0-9a-f]{7,40}$/;

export function releaseSha(): string | undefined {
  const value = typeof __RELEASE_SHA__ === 'undefined' ? undefined : __RELEASE_SHA__;
  return typeof value === 'string' && RELEASE_SHA_PATTERN.test(value) ? value : undefined;
}

export interface AcquisitionContext {
  /** Random per landing; present only when this document is attributed to a landing. */
  landingId?: string;
  sourceGroup: SourceGroup;
  pageFamily: PageFamily;
}

const DEFAULT_CONTEXT: AcquisitionContext = { sourceGroup: 'unknown', pageFamily: 'app' };
let context: AcquisitionContext = { ...DEFAULT_CONTEXT };

/** Held in memory only; read by the transport to complete the D-005 envelope. */
export function setAcquisitionContext(next: AcquisitionContext): void {
  context = { ...next };
}

export function getAcquisitionContext(): Readonly<AcquisitionContext> {
  return context;
}

const MAX_BODY_BYTES = 2048;
const RETRY_DELAY_MS = 1000;
const RETRY_WINDOW_MS = 10_000;

export interface FetchSinkOptions {
  /** Defaults to `globalThis.fetch`, resolved at send time. */
  fetchImpl?: typeof fetch;
  /** Defaults to a fresh random id: one per document. */
  sessionId?: string;
}

export function globalPrivacyControlSet(): boolean {
  return typeof navigator !== 'undefined' && (navigator as { globalPrivacyControl?: unknown }).globalPrivacyControl === true;
}

/**
 * The first-party sink. Sends each event once; retries once, after a short
 * delay and only within 10 s of the first attempt, on a network error or a
 * 5xx, with the identical body (same event_id). 4xx and 429 are final. No
 * queue, no persistence, nothing awaited by the caller.
 */
export function createFetchSink(options: FetchSinkOptions = {}): AcquisitionSink {
  const sessionId = options.sessionId ?? randomId();

  const send = (body: string, attempt: number, startedAt: number): void => {
    const retryable = () => {
      if (attempt === 0 && Date.now() - startedAt + RETRY_DELAY_MS <= RETRY_WINDOW_MS) {
        setTimeout(() => send(body, 1, startedAt), RETRY_DELAY_MS);
      }
    };
    try {
      const doFetch = options.fetchImpl ?? globalThis.fetch;
      doFetch(ACQUISITION_ENDPOINT, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body,
        keepalive: true,
        credentials: 'omit',
        referrerPolicy: 'no-referrer',
        cache: 'no-store',
        mode: 'same-origin',
      }).then(
        (response) => {
          if (response.status >= 500) retryable();
        },
        () => retryable(),
      );
    } catch {
      retryable();
    }
  };

  return {
    emit(event: AcquisitionEvent): void {
      try {
        if (globalPrivacyControlSet()) return;
        const sha = releaseSha();
        const ctx = context;
        const wire: Record<string, unknown> = {
          page_family: ctx.pageFamily,
          source_group: ctx.sourceGroup,
          ...(ctx.landingId ? { landing_id: ctx.landingId } : {}),
          ...(sha ? { release_sha: sha } : {}),
          ...event,
          session_id: sessionId,
        };
        const body = JSON.stringify(wire);
        if (body.length > MAX_BODY_BYTES) return;
        send(body, 0, Date.now());
      } catch {
        // Measurement must never break the flow it observes.
      }
    },
  };
}

/**
 * Install the fetch sink. A no-op unless `enabled` (default: the
 * ACQUISITION_COLLECTOR_ENABLED constant, false) and Global Privacy Control is
 * not set. Returns whether a transport was installed.
 */
export function installAcquisitionTransport(enabled: boolean = ACQUISITION_COLLECTOR_ENABLED): boolean {
  if (!enabled || globalPrivacyControlSet()) return false;
  activeSink = createFetchSink();
  return true;
}
