/**
 * User-facing copy + typed error for the v2 report API.
 *
 * mapErrorToMessage is the ONLY source of user-visible copy for a failed
 * report request -- reportApi.ts never surfaces a raw server-provided
 * `message` string to the UI. Two reasons: (1) consistent tone/wording
 * regardless of what a given backend build happens to say, and (2) this
 * table is claim_sweep-safe by construction (scripts/claim_sweep.py bans
 * AI/ML/personalisation claims in public-facing copy) -- a server-supplied
 * message is not vetted against that gate, so it must never reach the DOM.
 */
import type { ApiErrorCode } from '../types';

export class ReportApiError extends Error {
  code: ApiErrorCode | 'network_error' | 'invalid_response';
  status: number | null;
  correlationId: string | null;

  constructor(
    code: ApiErrorCode | 'network_error' | 'invalid_response',
    message: string,
    status: number | null = null,
    correlationId: string | null = null
  ) {
    super(message);
    this.name = 'ReportApiError';
    this.code = code;
    this.status = status;
    this.correlationId = correlationId;
    // Keep `instanceof ReportApiError` working after transpilation --
    // extending a built-in like Error can otherwise lose the prototype
    // chain depending on target/module settings.
    Object.setPrototypeOf(this, ReportApiError.prototype);
  }
}

const DEFAULT_MESSAGE = 'Something went wrong on our side — please try again.';

// Every ApiErrorCode, plus the two client-only codes (network_error,
// invalid_response) reportApi.ts produces itself. internal_error and
// undeclared_parameter intentionally share DEFAULT_MESSAGE -- neither is
// something a user can act on differently from a generic "try again".
const ERROR_MESSAGES: Record<ApiErrorCode | 'network_error' | 'invalid_response', string> = {
  invalid_registration: "That doesn't look like a valid UK registration — please check and try again.",
  vehicle_not_found:
    "We couldn't find that registration in the MOT database. Double-check the plate — brand-new vehicles may not appear yet.",
  dvsa_unavailable:
    'The official vehicle-data service is temporarily unavailable. Your details are fine — please try again in a few minutes.',
  rate_limited: 'Too many checks in a short time — please wait a minute and try again.',
  report_not_found: "That report link isn't valid — it may be incomplete.",
  report_expired: 'This report link has expired. Run a fresh check to get an up-to-date result.',
  storage_unavailable: 'Saved reports are temporarily unavailable — please try again shortly.',
  idempotency_conflict: 'This retry could not be matched safely. Please submit the check again.',
  internal_error: DEFAULT_MESSAGE,
  undeclared_parameter: DEFAULT_MESSAGE,
  network_error: "We couldn't reach AutoSafe — check your connection and try again.",
  invalid_response: 'We received an unexpected response — please try again.',
};

/**
 * Map an error code to user-facing copy. Total over `string`: any code this
 * table doesn't recognise falls back to DEFAULT_MESSAGE rather than
 * throwing -- this function's job is to always produce something safe to
 * render, never to validate (isApiErrorEnvelope's job, in reportValidation.ts).
 */
export function mapErrorToMessage(code: string): string {
  return Object.prototype.hasOwnProperty.call(ERROR_MESSAGES, code)
    ? ERROR_MESSAGES[code as keyof typeof ERROR_MESSAGES]
    : DEFAULT_MESSAGE;
}
