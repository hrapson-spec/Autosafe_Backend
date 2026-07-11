/**
 * AutoSafe v2 Report API client.
 *
 * Parallel to services/autosafeApi.ts's legacy /api/risk client -- this
 * talks to the v2 report endpoints (report_contract.py) instead. Nothing
 * here is wired into the UI yet (that happens in a later wave); this
 * module and its legacy sibling coexist unmodified until consumers switch
 * over.
 *
 * Every response is validated against isReportV2 / isApiErrorEnvelope
 * before this module hands it back to a caller -- a value that doesn't
 * satisfy those shapes always becomes a thrown ReportApiError, never a
 * best-effort coercion. See reportValidation.ts / errorMessages.ts.
 */
import type { ReportV2 } from '../types';
import { isApiErrorEnvelope, isReportV2 } from './reportValidation';
import { mapErrorToMessage, ReportApiError } from './errorMessages';

// Same resolution pattern as services/autosafeApi.ts: same-origin in
// production (empty string), overridable in dev via VITE_API_URL.
const API_BASE = import.meta.env.VITE_API_URL || '';

/**
 * fetch(), but a rejection (offline, DNS failure, CORS block -- anything
 * that never reached a server) becomes a typed ReportApiError instead of
 * an unhandled TypeError.
 */
async function safeFetch(url: string, init?: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch {
    throw new ReportApiError('network_error', mapErrorToMessage('network_error'));
  }
}

/**
 * Every non-2xx response, however it's shaped, becomes a thrown
 * ReportApiError -- never a return value. A response that validates as
 * ApiErrorEnvelope maps its error_code to this client's own copy (see
 * errorMessages.ts's module comment for why the server's `message` is
 * never used directly); anything else (a legacy `{detail: ...}` body, an
 * empty body, non-JSON) becomes 'invalid_response' with the HTTP status
 * preserved for logging/debugging.
 */
async function throwForErrorResponse(response: Response): Promise<never> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ReportApiError('invalid_response', mapErrorToMessage('invalid_response'), response.status);
  }

  if (isApiErrorEnvelope(body)) {
    throw new ReportApiError(
      body.error_code,
      mapErrorToMessage(body.error_code),
      response.status,
      body.correlation_id
    );
  }

  throw new ReportApiError('invalid_response', mapErrorToMessage('invalid_response'), response.status);
}

/**
 * Shared ok-path handling for both endpoints below: a non-ok response
 * always throws (see throwForErrorResponse); an ok response that doesn't
 * parse as JSON, or parses but doesn't satisfy isReportV2, throws
 * 'invalid_response' rather than handing a partially-trusted object to the
 * caller. On success, returns the parsed body as-is (no re-shaping), so
 * callers get the exact object isReportV2 validated.
 */
async function parseReportResponse(response: Response): Promise<ReportV2> {
  if (!response.ok) {
    return throwForErrorResponse(response);
  }

  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new ReportApiError('invalid_response', mapErrorToMessage('invalid_response'), response.status);
  }

  if (isReportV2(body)) {
    return body;
  }
  throw new ReportApiError('invalid_response', mapErrorToMessage('invalid_response'), response.status);
}

/**
 * Create (or idempotently fetch, if the same idempotency_key was already
 * used) a v2 report.
 *
 * postcode is sent only when truthy -- an empty string means "not given"
 * and is omitted entirely rather than sent as "" or null, matching
 * ReportCreateRequest.postcode's Optional[str] semantics on the backend.
 * mileageUser is sent whenever the caller actually passed a value,
 * including 0 -- checked with `!== undefined`, not truthiness, so a
 * genuine 0-mile entry (a brand-new car) is never dropped the way
 * `mileageUser || ...` would drop it.
 *
 * registration and postcode are never placed in the URL -- both travel
 * only in the POST body.
 */
export async function createReport(
  registration: string,
  postcode: string,
  mileageUser?: number
): Promise<ReportV2> {
  const body: {
    registration: string;
    postcode?: string;
    mileage_user?: number;
    idempotency_key: string;
  } = {
    registration,
    idempotency_key: crypto.randomUUID(),
  };
  if (postcode) {
    body.postcode = postcode;
  }
  if (mileageUser !== undefined) {
    body.mileage_user = mileageUser;
  }

  const response = await safeFetch(`${API_BASE}/api/v2/reports`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  return parseReportResponse(response);
}

/**
 * Fetch a previously-created report by its share token. The token is the
 * only thing that ever appears in the URL -- no registration, postcode, or
 * other PII travels via the query string or path beyond it.
 */
export async function getReport(token: string): Promise<ReportV2> {
  const response = await safeFetch(`${API_BASE}/api/v2/reports/${encodeURIComponent(token)}`, {
    method: 'GET',
  });

  return parseReportResponse(response);
}
